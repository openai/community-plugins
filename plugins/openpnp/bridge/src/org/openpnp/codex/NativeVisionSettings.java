/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.vision.*;
import org.openpnp.spi.PartAlignment;
import org.openpnp.vision.pipeline.*;
import org.openpnp.vision.pipeline.stages.*;

/** Native vision configuration only. Call under the bridge's idle executor, lease and revision fence. */
public final class NativeVisionSettings {
    private NativeVisionSettings() { }
    public static final List<String> TYPES = Collections.unmodifiableList(Arrays.asList(
        "clone_vision_settings", "set_bottom_vision_settings", "set_fiducial_vision_settings",
        "assign_vision_settings", "remove_vision_settings", "set_vision_pipeline_stage", "set_vision_parameter"));
    private static final int MAX_PROFILES = 512, MAX_HOLDERS = 20002, MAX_XML = 524288;
    // These are the pinned stock pipeline classes. No arbitrary class, script, image path or XML input is accepted.
    private static final Set<Class<?>> PIPELINE_CLASSES = Set.of(BlurGaussian.class, ConvertColor.class,
        DrawContours.class, DrawRotatedRects.class, FilterContours.class, FindContours.class,
        ImageCapture.class, ImageRecall.class, ImageWriteDebug.class, MaskCircle.class, MaskHsv.class,
        MinAreaRect.class, ParameterNumeric.class, Threshold.class, DetectRectlinearSymmetry.class,
        ParameterBool.class, ConvertModelToKeyPoints.class, DetectCircularSymmetry.class,
        DrawCircles.class, CreateFootprintTemplateImage.class, DrawKeyPoints.class,
        DrawTemplateMatches.class, MatchTemplate.class);

    public static final class Patch {
        private final State state;
        private final String before;
        private final List<Map<String,Object>> effects;
        private boolean used;
        private Patch(State state, String before, List<Map<String,Object>> effects) {
            this.state = state; this.before = before; this.effects = effects;
        }
        public List<Map<String,Object>> metadata() { return effects; }
        public synchronized void apply() throws Exception {
            if (used) fail("PATCH_ALREADY_APPLIED", "Stage a new vision patch before applying again");
            used = true;
            State current = new State(state.config);
            if (!before.equals(current.fingerprint()) || !state.sameIdentities(current))
                fail("STALE_MODEL", "Native vision settings, holders or inheritance changed since the preview");
            // All validation and cloning happened before any native setters. Setter failures are fenced by Bridge.
            for (String id : state.changed) if (state.profiles.containsKey(id)) {
                AbstractVisionSettings next = state.profiles.get(id), original = state.originals.get(id);
                if (original == null) state.config.addVisionSettings(next);
                else copyValues(next, original);
            }
            for (String key : state.changedBindings) {
                Holder holder = state.holders.get(key);
                holder.model.setBottomVisionSettings((BottomVisionSettings) state.actual(holder.bottom));
                holder.model.setFiducialVisionSettings((FiducialVisionSettings) state.actual(holder.fiducial));
            }
            for (String id : state.removed) state.config.removeVisionSettings(state.originals.get(id));
            state.config.fireVisionSettingsChanged();
        }
    }

    public static Patch stage(Configuration config, JsonArray changes) throws Exception {
        if (changes == null || changes.size() < 1 || changes.size() > 100)
            fail("INVALID_PATCH", "Expected one to one hundred vision changes");
        State state = new State(config); String before = state.fingerprint();
        for (JsonElement raw : changes) {
            if (!raw.isJsonObject()) fail("INVALID_ARGUMENT", "Every vision change must be an object");
            state.edit(raw.getAsJsonObject());
        }
        state.validateBindings();
        List<Map<String,Object>> effects = new ArrayList<>();
        for (String id : state.changed) effects.add(map("vision_settings_id", id,
            "change", state.removed.contains(id) ? "remove" : state.originals.containsKey(id) ? "update" : "clone",
            "affected_holders", state.effectiveUsers(id), "physical_qualification", false,
            "invalidation", List.of("job-validation", "vision-calibration-evidence", "registration", "motion-plan")));
        for (String key : state.changedBindings) effects.add(map("holder", key,
            "change", "inheritance", "bottom", state.effective(state.holders.get(key), false),
            "fiducial", state.effective(state.holders.get(key), true),
            "affected_holders", state.descendants(key), "physical_qualification", false));
        return new Patch(state, before, immutableEffects(effects));
    }

    /** A complete declared inheritance view; no pipeline is processed and no native images are captured. */
    public static Map<String,Object> describe(Configuration config) throws Exception {
        State state = new State(config); List<Map<String,Object>> profiles = new ArrayList<>(), holders = new ArrayList<>();
        for (AbstractVisionSettings settings : state.profiles.values()) {
            List<Map<String,Object>> stages = new ArrayList<>();
            for (CvStage stage : settings.getPipeline().getStages()) stages.add(map("name", stage.getName(),
                "native_class", stage.getClass().getName(), "enabled", stage.isEnabled(),
                "parameters", stageParameters(stage), "parameter_editable", editableStage(stage) && !overridden(settings.getPipeline(),stage)));
            profiles.add(map("vision_settings_id", settings.getId(), "kind", kind(settings), "name", settings.getName(),
                "enabled", settings.isEnabled(), "stock", settings.isStockSetting(),
                "parameters", profileParameters(settings), "pipeline_parameters", parameterViews(settings),
                "pipeline_sha256", digest(serialize(settings.getPipeline())), "stages", stages,
                "effective_users", state.effectiveUsers(settings.getId())));
        }
        for (Holder holder : state.holders.values()) holders.add(map("holder", holder.key, "parent", holder.parent,
            "direct_bottom", holder.bottom, "direct_fiducial", holder.fiducial,
            "effective_bottom", state.effective(holder, false), "effective_fiducial", state.effective(holder, true)));
        return map("profiles", profiles, "holders", holders, "bottom_alignment_enabled", state.bottom.isEnabled(),
            "scope", "native-model-settings", "execution_performed", false, "physical_qualification", false,
            "pipeline_edit_scope", "existing BlurGaussian, Threshold and MaskHsv scalar parameters only",
            "fiducial_enabled_semantics", "stored flag; the pinned locator does not use it as an execution gate");
    }

    private static final class Holder {
        final String key, parent; final PartSettingsHolder model;
        String bottom, fiducial;
        Holder(String key, String parent, PartSettingsHolder model) {
            this.key = key; this.parent = parent; this.model = model;
            this.bottom = id(model.getBottomVisionSettings()); this.fiducial = id(model.getFiducialVisionSettings());
        }
    }
    private static final class State {
        final Configuration config;
        final ReferenceBottomVision bottom;
        final ReferenceFiducialLocator fiducial;
        final Map<String,AbstractVisionSettings> profiles = new LinkedHashMap<>(), originals = new LinkedHashMap<>();
        final Map<String,Holder> holders = new LinkedHashMap<>();
        final Set<String> changed = new LinkedHashSet<>(), changedBindings = new LinkedHashSet<>(), removed = new LinkedHashSet<>();
        State(Configuration config) throws Exception {
            this.config = config;
            if (config == null || config.getMachine() == null || config.getMachine().getPartAlignments().size() != 1)
                fail("UNSUPPORTED_NATIVE_CLASS", "This adapter requires one ReferenceBottomVision alignment");
            PartAlignment alignment = config.getMachine().getPartAlignments().get(0);
            if (alignment.getClass() != ReferenceBottomVision.class || config.getMachine().getFiducialLocator() == null ||
                config.getMachine().getFiducialLocator().getClass() != ReferenceFiducialLocator.class)
                fail("UNSUPPORTED_NATIVE_CLASS", "Exact native bottom vision and fiducial locator are required");
            bottom = (ReferenceBottomVision) alignment; fiducial = (ReferenceFiducialLocator) config.getMachine().getFiducialLocator();
            if (config.getVisionSettings().size() > MAX_PROFILES || config.getParts().size()+config.getPackages().size()+2 > MAX_HOLDERS)
                fail("CAPACITY_EXCEEDED", "Vision inventory exceeds this adapter's bounded profile");
            for (AbstractVisionSettings settings : config.getVisionSettings()) {
                validateProfile(settings); String id = settings.getId();
                if (profiles.put(id, settings) != null) fail("INVALID_MODEL", "Duplicate vision identity");
                originals.put(id, settings);
            }
            holders.put("machine:bottom", new Holder("machine:bottom", null, bottom));
            holders.put("machine:fiducial", new Holder("machine:fiducial", null, fiducial));
            for (org.openpnp.model.Package pkg : config.getPackages()) {
                if (pkg.getClass() != org.openpnp.model.Package.class) fail("UNSUPPORTED_NATIVE_CLASS", "Custom package holder is unsupported");
                holders.put("package:"+pkg.getId(), new Holder("package:"+pkg.getId(), "machine", pkg));
            }
            for (Part part : config.getParts()) {
                if (part.getPackage()!=null && config.getPackage(part.getPackage().getId())!=part.getPackage()) fail("INVALID_VISION_REFERENCE", "Part refers to a detached package identity");
                if (part.getClass() != Part.class) fail("UNSUPPORTED_NATIVE_CLASS", "Custom part holder is unsupported");
                holders.put("part:"+part.getId(), new Holder("part:"+part.getId(), part.getPackage()==null ? null : "package:"+part.getPackage().getId(), part));
            }
            for (Holder holder : holders.values()) {
                if (holder.model.getBottomVisionSettings()!=null && config.getVisionSettings(holder.bottom)!=holder.model.getBottomVisionSettings()) fail("INVALID_VISION_REFERENCE","Detached bottom vision reference");
                if (holder.model.getFiducialVisionSettings()!=null && config.getVisionSettings(holder.fiducial)!=holder.model.getFiducialVisionSettings()) fail("INVALID_VISION_REFERENCE","Detached fiducial vision reference");
            }
            validateBindings();
        }
        boolean sameIdentities(State current) {
            if (!originals.keySet().equals(current.originals.keySet()) || !holders.keySet().equals(current.holders.keySet())) return false;
            for (String id : originals.keySet()) if (originals.get(id) != current.originals.get(id)) return false;
            for (String key : holders.keySet()) if (holders.get(key).model != current.holders.get(key).model) return false;
            return true;
        }
        String fingerprint() throws Exception {
            StringBuilder value = new StringBuilder();
            for (AbstractVisionSettings p : profiles.values()) value.append(digest(xml(p))).append('\n');
            for (Holder h : holders.values()) value.append(new Gson().toJson(map("key",h.key,"parent",h.parent,"bottom",h.bottom,"fiducial",h.fiducial))).append('\n');
            value.append(bottom.isEnabled());
            return digest(value.toString());
        }
        AbstractVisionSettings actual(String id) { return id == null ? null : originals.containsKey(id) ? originals.get(id) : profiles.get(id); }
        AbstractVisionSettings setting(String id) throws Exception {
            AbstractVisionSettings value = profiles.get(id);
            if (value == null) fail("NOT_FOUND", "Vision settings identity does not exist: "+id);
            return value;
        }
        AbstractVisionSettings editable(String id) throws Exception {
            AbstractVisionSettings value = setting(id);
            if (value.isStockSetting()) fail("STOCK_SETTINGS_IMMUTABLE", "Clone stock vision settings before editing");
            if (!changed.contains(id)) { value = copy(value); profiles.put(id,value); changed.add(id); }
            return value;
        }
        Map<String,Object> effective(Holder holder, boolean fid) {
            Set<String> visited = new HashSet<>(); Holder cursor = holder;
            while (cursor != null && visited.add(cursor.key)) {
                String value = fid ? cursor.fiducial : cursor.bottom;
                if (value != null) return map("vision_settings_id", value, "source_holder", cursor.key);
                String parent = cursor.parent;
                if ("machine".equals(parent)) parent = fid ? "machine:fiducial" : "machine:bottom";
                cursor = holders.get(parent);
            }
            return map("vision_settings_id", null, "source_holder", null);
        }
        List<String> effectiveUsers(String id) {
            List<String> users = new ArrayList<>();
            for (Holder h : holders.values()) if (id.equals(effective(h,false).get("vision_settings_id")) || id.equals(effective(h,true).get("vision_settings_id"))) users.add(h.key);
            return users;
        }
        List<String> descendants(String key) {
            List<String> users = new ArrayList<>();
            for (Holder h : holders.values()) {
                Holder cursor = h; Set<String> visited = new HashSet<>();
                while (cursor != null && visited.add(cursor.key)) {
                    if (cursor.key.equals(key) || ("machine".equals(cursor.parent) && key.startsWith("machine:"))) { users.add(h.key); break; }
                    cursor = holders.get(cursor.parent);
                }
            }
            return users;
        }
        void validateBindings() throws Exception {
            for (Holder h : holders.values()) {
                if (h.bottom != null && !(profiles.get(h.bottom) instanceof BottomVisionSettings)) fail("INVALID_VISION_REFERENCE", "Dangling or mismatched bottom vision reference on "+h.key);
                if (h.fiducial != null && !(profiles.get(h.fiducial) instanceof FiducialVisionSettings)) fail("INVALID_VISION_REFERENCE", "Dangling or mismatched fiducial reference on "+h.key);
            }
            if (holders.get("machine:bottom").bottom == null || holders.get("machine:fiducial").fiducial == null)
                fail("INVALID_VISION_REFERENCE", "The machine roots must keep an explicit vision setting");
        }
        void edit(JsonObject c) throws Exception {
            String type = text(c,"type");
            switch (type) {
            case "clone_vision_settings": {
                only(c,"type","source_vision_settings_id","vision_settings_id","name");
                String id=text(c,"vision_settings_id");
                if (!id.matches("[A-Za-z][A-Za-z0-9_-]{0,127}") || id.contains("Stock")) fail("INVALID_ARGUMENT","New vision ID must be a bounded non-stock native ID");
                for (String existing : profiles.keySet()) if (existing.equalsIgnoreCase(id)) fail("ALREADY_EXISTS","Vision IDs are case-insensitive");
                for (String existing : originals.keySet()) if (existing.equalsIgnoreCase(id)) fail("ALREADY_EXISTS","Removed native identities cannot be recreated with a case variant");
                if (originals.containsKey(id) || profiles.size() >= MAX_PROFILES) fail("CAPACITY_EXCEEDED","Cannot reuse a removed identity or exceed profile capacity");
                AbstractVisionSettings next=copy(setting(text(c,"source_vision_settings_id"))); next.setId(id);next.setName(text(c,"name"));
                profiles.put(id,next);changed.add(id);break;
            }
            case "remove_vision_settings": {
                only(c,"type","vision_settings_id");String id=text(c,"vision_settings_id");editable(id);
                if (!originals.containsKey(id)) fail("INVALID_PATCH","Do not create and remove the same identity in one patch");
                profiles.remove(id);removed.add(id);break;
            }
            case "assign_vision_settings": {
                only(c,"type","holder","kind","vision_settings_id");String key=text(c,"holder"),kind=text(c,"kind");
                Holder holder=holders.get(key);if(holder==null)fail("NOT_FOUND","Unknown exact native vision holder");
                boolean fid;if(kind.equals("fiducial"))fid=true;else if(kind.equals("bottom"))fid=false;else {fail("INVALID_ARGUMENT","Vision kind must be bottom or fiducial");return;}
                if (key.startsWith("machine:") && !key.equals(fid?"machine:fiducial":"machine:bottom")) fail("INVALID_ARGUMENT","Select the matching machine vision root");
                if(!c.has("vision_settings_id"))fail("INVALID_ARGUMENT","Explicit vision ID or null is required");
                String id=c.get("vision_settings_id").isJsonNull()?null:text(c,"vision_settings_id");
                if(id!=null&&!kind(setting(id)).equals(kind))fail("INVALID_VISION_REFERENCE","Vision profile kind does not match assignment");
                if(fid)holder.fiducial=id;else holder.bottom=id;changedBindings.add(key);break;
            }
            case "set_bottom_vision_settings": {
                only(c,"type","vision_settings_id","enabled","pre_rotate_usage","part_size_check","size_tolerance_percent","max_rotation","asymmetric","offset_x_mm","offset_y_mm");
                AbstractVisionSettings base=editable(text(c,"vision_settings_id"));if(base.getClass()!=BottomVisionSettings.class)fail("INVALID_VISION_REFERENCE","Expected bottom vision settings");
                BottomVisionSettings next=(BottomVisionSettings)base;
                next.setEnabled(bool(c,"enabled"));next.setPreRotateUsage(enumeration(c,"pre_rotate_usage",ReferenceBottomVision.PreRotateUsage.class));
                next.setCheckPartSizeMethod(enumeration(c,"part_size_check",ReferenceBottomVision.PartSizeCheckMethod.class));
                next.setCheckSizeTolerancePercent(integer(c,"size_tolerance_percent",0,100));next.setMaxRotation(enumeration(c,"max_rotation",ReferenceBottomVision.MaxRotation.class));
                boolean asym=bool(c,"asymmetric");double x=number(c,"offset_x_mm",-50,50),y=number(c,"offset_y_mm",-50,50);
                if(!asym&&(x!=0||y!=0))fail("INVALID_ARGUMENT","Symmetric settings must have zero offsets");
                next.setAsymmetric(asym);next.setVisionOffset(new Location(LengthUnit.Millimeters,x,y,0,0));break;
            }
            case "set_fiducial_vision_settings": {
                only(c,"type","vision_settings_id","enabled","max_vision_passes","max_linear_offset_mm","parallax_diameter_mm","parallax_angle_deg");
                AbstractVisionSettings base=editable(text(c,"vision_settings_id"));if(base.getClass()!=FiducialVisionSettings.class)fail("INVALID_VISION_REFERENCE","Expected fiducial settings");
                FiducialVisionSettings next=(FiducialVisionSettings)base;next.setEnabled(bool(c,"enabled"));next.setMaxVisionPasses(integer(c,"max_vision_passes",1,10));
                next.setMaxLinearOffset(new Length(number(c,"max_linear_offset_mm",0,20),LengthUnit.Millimeters));
                next.setParallaxDiameter(new Length(number(c,"parallax_diameter_mm",0,50),LengthUnit.Millimeters));next.setParallaxAngle(number(c,"parallax_angle_deg",-180,180));break;
            }
            case "set_vision_pipeline_stage": {
                only(c,"type","vision_settings_id","stage_name","stage_type","parameters");
                AbstractVisionSettings next=editable(text(c,"vision_settings_id"));CvStage stage=next.getPipeline().getStage(text(c,"stage_name"));
                if(stage==null)fail("NOT_FOUND","Stage name does not exist in the selected native pipeline");
                if(!c.has("parameters")||!c.get("parameters").isJsonObject())fail("INVALID_ARGUMENT","Stage parameters must be an object");
                if(overridden(next.getPipeline(),stage))fail("VISION_PARAMETER_OVERRIDDEN","Use the explicit parameter assignment when a native parameter controls this stage; runtime overrides require a separate recipe");
                editStage(stage,text(c,"stage_type"),c.getAsJsonObject("parameters"));break;
            }
            case "set_vision_parameter": {
                only(c,"type","vision_settings_id","parameter_name","value");
                AbstractVisionSettings next=editable(text(c,"vision_settings_id"));
                String name=text(c,"parameter_name");CvStage raw=next.getPipeline().getStage(name);
                CvAbstractParameterStage parameter=parameterTarget(next,raw);
                Object value=null;
                if(!c.get("value").isJsonNull()) {
                    if(raw.getClass()==ParameterBool.class)value=bool(c,"value");
                    else {
                        ParameterNumeric numeric=(ParameterNumeric)raw;
                        double number=number(c,"value",numeric.getMinimumValue(),numeric.getMaximumValue());
                        if(number!=Math.rint(number))fail("INVALID_ARGUMENT","This native target requires an exact integer");
                        if(number<0||number>255)fail("OUT_OF_RANGE","Effective threshold must stay within 0–255");
                        if(((Enum<?>)numeric.getNumericType()).name().equals("Integer"))value=Integer.valueOf((int)number);else value=Double.valueOf(number);
                    }
                }
                Map<String,Object> assignments=new LinkedHashMap<>();
                if(next.getPipelineParameterAssignments()!=null)assignments.putAll(next.getPipelineParameterAssignments());
                if(value==null)assignments.remove(name);else assignments.put(name,value);
                if(assignments.size()>32)fail("CAPACITY_EXCEEDED","Too many pipeline parameter assignments");
                next.setPipelineParameterAssignments(assignments.isEmpty()?null:assignments);
                next.getPipeline().setProperty(name,value);parameter.process(next.getPipeline());
                Threshold target=(Threshold)next.getPipeline().getStage(parameter.getStageName());
                if(target.getThreshold()<0||target.getThreshold()>255)fail("OUT_OF_RANGE","Effective threshold exceeds 0–255");
                break;
            }
            default: fail("UNSUPPORTED_SETTING","Unknown typed vision change");
            }
        }
    }

    private static boolean overridden(CvPipeline pipeline,CvStage stage) {
        if(stage.getClass()==BlurGaussian.class && nonempty(((BlurGaussian)stage).getPropertyName()))return true;
        if(stage.getClass()==MaskHsv.class && nonempty(((MaskHsv)stage).getPropertyName()))return true;
        for(CvStage other:pipeline.getStages()) if(other instanceof CvAbstractParameterStage && other.isEnabled()) {
            CvAbstractParameterStage parameter=(CvAbstractParameterStage)other;
            if(stage.getName().equals(parameter.getStageName()) && nonempty(parameter.getPropertyName()) && nonempty(parameter.getParameterLabel()))return true;
        }
        return false;
    }
    private static boolean nonempty(String value){return value!=null&&!value.isEmpty();}
    private static CvAbstractParameterStage parameterTarget(AbstractVisionSettings settings,CvStage raw)throws Exception {
        if(raw==null)fail("NOT_FOUND","Named parameter stage does not exist");
        if((raw.getClass()!=ParameterNumeric.class && raw.getClass()!=ParameterBool.class) || !raw.isEnabled())fail("UNSUPPORTED_SETTING","Expected an enabled native numeric or boolean parameter");
        CvAbstractParameterStage parameter=(CvAbstractParameterStage)raw;
        if(parameter.parameterName()==null||!nonempty(parameter.getParameterLabel()))fail("UNSUPPORTED_SETTING","Parameter has no stable active name or label");
        CvStage target=settings.getPipeline().getStage(parameter.getStageName());
        if(target==null||target.getClass()!=Threshold.class)fail("UNSUPPORTED_SETTING","Parameter assignments currently support native Threshold targets only");
        if(settings.getPipeline().getStages().indexOf(raw)>=settings.getPipeline().getStages().indexOf(target))fail("UNSUPPORTED_SETTING","Parameter must run before its target stage");
        if(raw.getClass()==ParameterBool.class) {
            if(!Set.of("auto","invert").contains(parameter.getPropertyName()))fail("UNSUPPORTED_SETTING","Boolean assignment requires threshold auto or invert");
        } else {
            ParameterNumeric numeric=(ParameterNumeric)raw;String type=((Enum<?>)numeric.getNumericType()).name();
            if(((Threshold)target).isAuto())fail("VISION_PARAMETER_OVERRIDDEN","Automatic thresholding overrides the numeric threshold");
            for(CvStage other:settings.getPipeline().getStages())if(other instanceof CvAbstractParameterStage&&other.isEnabled()) {
                CvAbstractParameterStage control=(CvAbstractParameterStage)other;
                if(target.getName().equals(control.getStageName())&&"auto".equals(control.getPropertyName())&&nonempty(control.getParameterLabel()))fail("VISION_PARAMETER_OVERRIDDEN","Another active parameter controls automatic thresholding");
            }
            if(!Set.of("Integer","Double").contains(type)||!"threshold".equals(parameter.getPropertyName()))fail("UNSUPPORTED_SETTING","Numeric assignment requires a direct threshold value");
            if(!Double.isFinite(numeric.getMinimumValue())||!Double.isFinite(numeric.getMaximumValue())||numeric.getMinimumValue()>numeric.getMaximumValue())fail("INVALID_MODEL","Parameter bounds are invalid");
        }
        for(CvStage other:settings.getPipeline().getStages()) if(other!=raw && other instanceof CvAbstractParameterStage && other.isEnabled()) {
            CvAbstractParameterStage p=(CvAbstractParameterStage)other;
            if(Objects.equals(p.getStageName(),parameter.getStageName())&&Objects.equals(p.getPropertyName(),parameter.getPropertyName())&&nonempty(p.getParameterLabel()))fail("VISION_PARAMETER_OVERRIDDEN","Multiple active parameters target the same property");
        }
        return parameter;
    }
    private static List<Map<String,Object>> parameterViews(AbstractVisionSettings settings) {
        List<Map<String,Object>> result=new ArrayList<>();
        for(CvStage raw:settings.getPipeline().getStages())if(raw instanceof CvAbstractParameterStage) {
            CvAbstractParameterStage p=(CvAbstractParameterStage)raw;boolean editable=true;
            try{parameterTarget(settings,raw);}catch(Exception e){editable=false;}
            Object defaultValue=p.defaultParameterValue();
            // Unit-bearing native defaults are described as unavailable to this scalar adapter.
            if(!(defaultValue instanceof Number||defaultValue instanceof Boolean||defaultValue instanceof String))defaultValue=null;
            Map<String,Object> assignments=settings.getPipelineParameterAssignments();
            result.add(map("parameter_name",p.parameterName(),"target_stage",p.getStageName(),"target_property",p.getPropertyName(),
                "default_value",defaultValue,"assigned_value",assignments==null?null:assignments.get(p.parameterName()),"value_editable",editable,
                "minimum",raw.getClass()==ParameterNumeric.class?((ParameterNumeric)raw).getMinimumValue():null,
                "maximum",raw.getClass()==ParameterNumeric.class?((ParameterNumeric)raw).getMaximumValue():null,
                "native_value_type",raw.getClass()==ParameterNumeric.class?((Enum<?>)((ParameterNumeric)raw).getNumericType()).name():"Boolean"));
        }
        return result;
    }
    private static Map<String,Object> profileParameters(AbstractVisionSettings raw)throws Exception {
        if(raw.getClass()==BottomVisionSettings.class){BottomVisionSettings s=(BottomVisionSettings)raw;Location offset=s.getVisionOffset().convertToUnits(LengthUnit.Millimeters);
            return map("enabled",s.isEnabled(),"pre_rotate_usage",s.getPreRotateUsage().name(),"part_size_check",s.getCheckPartSizeMethod().name(),"size_tolerance_percent",s.getCheckSizeTolerancePercent(),"max_rotation",s.getMaxRotation().name(),"asymmetric",((BottomVisionSettings)copy(s)).isAsymmetric(),"offset_x_mm",offset.getX(),"offset_y_mm",offset.getY());
        }
        FiducialVisionSettings s=(FiducialVisionSettings)raw;
        return map("enabled",s.isEnabled(),"max_vision_passes",s.getMaxVisionPasses(),"max_linear_offset_mm",s.getMaxLinearOffset().convertToUnits(LengthUnit.Millimeters).getValue(),"parallax_diameter_mm",s.getParallaxDiameter().convertToUnits(LengthUnit.Millimeters).getValue(),"parallax_angle_deg",s.getParallaxAngle());
    }
    private static String serialize(Object value)throws Exception {StringWriter out=new StringWriter();Configuration.createSerializer().write(value,out);String text=out.toString();if(text.getBytes(StandardCharsets.UTF_8).length>MAX_XML)fail("CAPACITY_EXCEEDED","Serialized native pipeline exceeds 512 KiB");return text;}

    private static void editStage(CvStage stage,String type,JsonObject c) throws Exception {
        if(stage.getClass()==BlurGaussian.class&&type.equals("gaussian")) {
            only(c,"kernel_size");int kernel=integer(c,"kernel_size",3,99);if(kernel%2==0)fail("INVALID_ARGUMENT","Gaussian kernel must be odd");((BlurGaussian)stage).setKernelSize(kernel);
        } else if(stage.getClass()==Threshold.class&&type.equals("threshold")) {
            only(c,"threshold","auto","invert");Threshold next=(Threshold)stage;next.setThreshold(integer(c,"threshold",0,255));next.setAuto(bool(c,"auto"));next.setInvert(bool(c,"invert"));
        } else if(stage.getClass()==MaskHsv.class&&type.equals("hsv")) {
            only(c,"hue_min","hue_max","saturation_min","saturation_max","value_min","value_max","invert");MaskHsv next=(MaskHsv)stage;
            int h0=integer(c,"hue_min",0,255),h1=integer(c,"hue_max",0,255),s0=integer(c,"saturation_min",0,255),s1=integer(c,"saturation_max",0,255),v0=integer(c,"value_min",0,255),v1=integer(c,"value_max",0,255);
            if(h0>h1||s0>s1||v0>v1)fail("INVALID_ARGUMENT","HSV minimum must not exceed maximum; use explicit inversion for excluded hue ranges");
            if(Boolean.TRUE.equals(next.getAuto()))fail("UNSUPPORTED_SETTING","Automatic HSV masks must be configured through a separate measured recipe");
            next.setHueMin(h0);next.setHueMax(h1);next.setSaturationMin(s0);next.setSaturationMax(s1);next.setValueMin(v0);next.setValueMax(v1);next.setInvert(bool(c,"invert"));
        } else fail("UNSUPPORTED_SETTING","Stage type/class mismatch or unsupported stage parameters");
    }
    private static boolean editableStage(CvStage s) {return s.getClass()==BlurGaussian.class||s.getClass()==Threshold.class||(s.getClass()==MaskHsv.class&&!Boolean.TRUE.equals(((MaskHsv)s).getAuto()));}
    private static Map<String,Object> stageParameters(CvStage s) {
        if(s.getClass()==BlurGaussian.class)return map("kernel_size",((BlurGaussian)s).getKernelSize(),"property_override",((BlurGaussian)s).getPropertyName());
        if(s.getClass()==Threshold.class){Threshold t=(Threshold)s;return map("threshold",t.getThreshold(),"auto",t.isAuto(),"invert",t.isInvert());}
        if(s.getClass()==MaskHsv.class){MaskHsv m=(MaskHsv)s;return map("hue_min",m.getHueMin(),"hue_max",m.getHueMax(),"saturation_min",m.getSaturationMin(),"saturation_max",m.getSaturationMax(),"value_min",m.getValueMin(),"value_max",m.getValueMax(),"invert",m.getInvert(),"auto",m.getAuto(),"property_override",m.getPropertyName());}
        // The pinned Gson constructs adapters for concrete map classes even on
        // write. Use a detached public map, not JDK-private Collections.EmptyMap,
        // so configuration snapshots work with the launcher's module access.
        return new LinkedHashMap<>();
    }
    private static void validateProfile(AbstractVisionSettings s) throws Exception {
        if(s.getClass()!=BottomVisionSettings.class&&s.getClass()!=FiducialVisionSettings.class)fail("UNSUPPORTED_NATIVE_CLASS","Custom vision settings are unsupported");
        if(s.getId()==null||s.getId().length()>128||s.getId().isEmpty())fail("INVALID_MODEL","Missing or oversized native vision ID");
        if(s.getPipeline().getStages().size()>100)fail("CAPACITY_EXCEEDED","Native pipeline exceeds 100 stages");
        Set<String> names=new HashSet<>();for(CvStage stage:s.getPipeline().getStages()) {
            if(!PIPELINE_CLASSES.contains(stage.getClass()))fail("UNSUPPORTED_NATIVE_CLASS","Pipeline contains a class outside the pinned stock profile");
            if(stage.getName()==null||stage.getName().isEmpty()||stage.getName().length()>128||!names.add(stage.getName()))fail("INVALID_MODEL","Pipeline stage names must be unique and bounded");
        }
        Map<String,Object> parameters=s.getPipelineParameterAssignments();if(parameters!=null) {
            if(parameters.size()>32)fail("CAPACITY_EXCEEDED","Too many pipeline parameter assignments");
            for(Map.Entry<String,Object> entry:parameters.entrySet()) {
                Object value=entry.getValue();if(entry.getKey()==null||entry.getKey().length()>128||value==null||
                    !(value instanceof Boolean||value instanceof String||value instanceof Number))fail("UNSUPPORTED_SETTING","Only scalar existing parameter assignments are supported");
                if(value instanceof String&&((String)value).length()>4096)fail("CAPACITY_EXCEEDED","Oversized parameter value");
                if(value instanceof Number&&!Double.isFinite(((Number)value).doubleValue()))fail("INVALID_MODEL","Nonfinite parameter value");
            }
        }
        xml(s);
    }
    private static AbstractVisionSettings copy(AbstractVisionSettings s) throws Exception {
        validateProfile(s);String source=xml(s);AbstractVisionSettings copy=Configuration.createSerializer().read(s.getClass(),source);
        if(!source.equals(xml(copy)))fail("UNSUPPORTED_SETTING","Native cloning would migrate serialized settings; migrate locally before planning");
        return copy;
    }
    private static void copyValues(AbstractVisionSettings from,AbstractVisionSettings to) {
        to.setName(from.getName());to.setEnabled(from.isEnabled());to.setPipeline(from.getPipeline());
        to.setPipelineParameterAssignments(from.getPipelineParameterAssignments()==null?null:new LinkedHashMap<>(from.getPipelineParameterAssignments()));
        if(from.getClass()==BottomVisionSettings.class){BottomVisionSettings f=(BottomVisionSettings)from,t=(BottomVisionSettings)to;
            t.setPreRotateUsage(f.getPreRotateUsage());t.setCheckPartSizeMethod(f.getCheckPartSizeMethod());t.setCheckSizeTolerancePercent(f.getCheckSizeTolerancePercent());t.setMaxRotation(f.getMaxRotation());t.setAsymmetric(f.isAsymmetric());t.setVisionOffset(f.getVisionOffset());
        }else{FiducialVisionSettings f=(FiducialVisionSettings)from,t=(FiducialVisionSettings)to;t.setMaxVisionPasses(f.getMaxVisionPasses());t.setMaxLinearOffset(f.getMaxLinearOffset());t.setParallaxDiameter(f.getParallaxDiameter());t.setParallaxAngle(f.getParallaxAngle());}
    }
    private static String xml(AbstractVisionSettings s) throws Exception {StringWriter out=new StringWriter();Configuration.createSerializer().write(s,out);String value=out.toString();if(value.getBytes(StandardCharsets.UTF_8).length>MAX_XML)fail("CAPACITY_EXCEEDED","Serialized vision profile exceeds 512 KiB");return value;}
    private static String digest(String s) throws Exception {byte[] bytes=MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8));StringBuilder b=new StringBuilder();for(byte v:bytes)b.append(String.format("%02x",v&255));return b.toString();}
    private static String id(AbstractVisionSettings value){return value==null?null:value.getId();}
    private static String kind(AbstractVisionSettings value){return value.getClass()==BottomVisionSettings.class?"bottom":"fiducial";}
    private static Map<String,Object> map(Object... pairs){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;}
    @SuppressWarnings("unchecked") private static List<Map<String,Object>> immutableEffects(List<Map<String,Object>> effects) {
        // Metadata exposes no native object references. Deep immutability prevents callers changing reviewed effects.
        return (List<Map<String,Object>>)(List<?>)freeze(effects);
    }
    private static Object freeze(Object value){if(value instanceof Map){Map<String,Object> m=new LinkedHashMap<>();((Map<?,?>)value).forEach((k,v)->m.put((String)k,freeze(v)));return Collections.unmodifiableMap(m);}if(value instanceof List){List<Object> a=new ArrayList<>();for(Object v:(List<?>)value)a.add(freeze(v));return Collections.unmodifiableList(a);}return value;}
    private static void only(JsonObject c,String... keys)throws Exception{Set<String> allowed=Set.of(keys);for(Map.Entry<String,JsonElement> entry:c.entrySet())if(!allowed.contains(entry.getKey()))fail("UNKNOWN_FIELD","Unexpected vision field: "+entry.getKey());for(String key:keys)if(!c.has(key))fail("INVALID_ARGUMENT","Missing vision field: "+key);}
    private static String text(JsonObject c,String key)throws Exception{JsonElement v=c.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isString()||v.getAsString().isEmpty()||v.getAsString().length()>256)fail("INVALID_ARGUMENT","Expected bounded string: "+key);return v.getAsString();}
    private static boolean bool(JsonObject c,String key)throws Exception{JsonElement v=c.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isBoolean())fail("INVALID_ARGUMENT","Expected boolean: "+key);return v.getAsBoolean();}
    private static double number(JsonObject c,String key,double low,double high)throws Exception{JsonElement v=c.get(key);if(v==null||!v.isJsonPrimitive()||!v.getAsJsonPrimitive().isNumber())fail("INVALID_ARGUMENT","Expected number: "+key);double n=v.getAsDouble();if(!Double.isFinite(n)||n<low||n>high)fail("OUT_OF_RANGE","Vision field out of range: "+key);return n;}
    private static int integer(JsonObject c,String key,int low,int high)throws Exception{double n=number(c,key,low,high);if(n!=Math.rint(n))fail("INVALID_ARGUMENT","Expected integer: "+key);return (int)n;}
    private static <E extends Enum<E>> E enumeration(JsonObject c,String key,Class<E> type)throws Exception{try{return Enum.valueOf(type,text(c,key));}catch(IllegalArgumentException e){fail("INVALID_ENUM","Unknown native vision enum: "+key);return null;}}
    private static void fail(String code,String message)throws Bridge.Fault{throw new Bridge.Fault(code,message);}
}
